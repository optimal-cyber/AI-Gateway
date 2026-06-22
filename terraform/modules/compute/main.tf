# =============================================================================
# compute — two app hosts: chat-host (Open WebUI) and gateway-host (LiteLLM)
# =============================================================================
# - No SSH key, no public IP (ADR-006, requirement #1). Access via SSM only.
# - IMDSv2 required (T-AWS-S).
# - Instance role: SSM + GetSecretValue on lab/* + CloudWatch Logs write.
# - Egress: app subnet has no internet route; user-data points dnf/docker/SSM
#   at the Squid proxy (ADR-009). In networkfirewall mode, proxy_private_ip is
#   empty and traffic routes through the firewall endpoint instead.
# =============================================================================

locals {
  hosts = {
    "chat-host"    = var.chat_log_group
    "gateway-host" = var.gateway_log_group
  }

  proxy_ip = var.proxy_private_ip == null ? "" : var.proxy_private_ip
}

# ---- security group ---------------------------------------------------------
# No inbound from the internet ever. Only intra-app-subnet 4000 (chat->gateway).
# Egress restricted to the VPC: the only way out is the proxy (a VPC address);
# the proxy egresses to the internet, not these hosts.
resource "aws_security_group" "app" {
  name        = "${var.project_name}-app-sg"
  description = "App hosts: no public ingress; 4000/4001 intra-subnet; egress to VPC only."
  vpc_id      = var.vpc_id

  tags = { Name = "${var.project_name}-app-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "litellm_4000" {
  count             = length(var.app_subnet_cidrs)
  security_group_id = aws_security_group.app.id
  description       = "LiteLLM gateway port from app subnet ${count.index} (chat-host to gateway-host)"
  cidr_ipv4         = var.app_subnet_cidrs[count.index]
  from_port         = 4000
  to_port           = 4000
  ip_protocol       = "tcp"
}

# The gateway façade (gateway/) is the front door: Open WebUI and the Cloudflare
# tunnel reach it on 4001 intra-subnet. LiteLLM (4000) stays internal behind it.
resource "aws_vpc_security_group_ingress_rule" "gateway_facade_4001" {
  count             = length(var.app_subnet_cidrs)
  security_group_id = aws_security_group.app.id
  description       = "Gateway facade port from app subnet ${count.index} (chat-host + tunnel to gateway-host)"
  cidr_ipv4         = var.app_subnet_cidrs[count.index]
  from_port         = 4001
  to_port           = 4001
  ip_protocol       = "tcp"
}

# Intra-VPC egress is open (reaches proxy:3128, gateway:4000, VPC DNS).
resource "aws_vpc_security_group_egress_rule" "to_vpc" {
  security_group_id = aws_security_group.app.id
  description       = "All intra-VPC egress (proxy 3128, gateway 4000, DNS)"
  cidr_ipv4         = var.no_proxy_cidr
  ip_protocol       = "-1"
}

# The ONLY direct-to-internet egress: cloudflared tunnel (port 7844). cloudflared
# cannot use an HTTP proxy, so its QUIC/HTTP2 transport goes straight to the
# Cloudflare edge. Crucially, 80/443 are NOT opened to the internet here, so all
# HTTP/HTTPS egress is forced through the Squid allowlist proxy (ADR-009).
# Hardening TODO: scope these to Cloudflare's published edge IP ranges.
resource "aws_vpc_security_group_egress_rule" "cloudflared_quic" {
  security_group_id = aws_security_group.app.id
  description       = "cloudflared tunnel (QUIC) to Cloudflare edge"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 7844
  to_port           = 7844
  ip_protocol       = "udp"
}

resource "aws_vpc_security_group_egress_rule" "cloudflared_http2" {
  security_group_id = aws_security_group.app.id
  description       = "cloudflared tunnel (HTTP2 fallback) to Cloudflare edge"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 7844
  to_port           = 7844
  ip_protocol       = "tcp"
}

# ---- IAM --------------------------------------------------------------------
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${var.project_name}-app-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = { Name = "${var.project_name}-app-role" }
}

resource "aws_iam_role_policy_attachment" "app_ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Scoped to lab/* secrets and the two app log groups (requirement #4, #7).
data "aws_iam_policy_document" "app_inline" {
  statement {
    sid       = "ReadLabSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = ["arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:lab/*"]
  }

  statement {
    sid     = "ShipLogs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:${var.chat_log_group}",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:${var.chat_log_group}:*",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:${var.gateway_log_group}",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:${var.gateway_log_group}:*",
    ]
  }
}

resource "aws_iam_role_policy" "app_inline" {
  name   = "${var.project_name}-app-inline"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app_inline.json
}

resource "aws_iam_instance_profile" "app" {
  name = "${var.project_name}-app-profile"
  role = aws_iam_role.app.name
}

# ---- instances --------------------------------------------------------------
resource "aws_instance" "app" {
  for_each = local.hosts

  ami                         = var.ami_id
  instance_type               = lookup(var.instance_type_overrides, each.key, var.instance_type)
  subnet_id                   = var.app_subnet_id
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.app.name
  associate_public_ip_address = false
  # No key_name — SSH disabled by design (ADR-006).

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # containers may hop once for creds
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_gb
    encrypted   = true
  }

  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    role           = each.key
    log_group_name = each.value
    proxy_ip       = local.proxy_ip
    no_proxy_cidr  = var.no_proxy_cidr
    region         = var.region
  })

  tags = {
    Name = "${var.project_name}-${each.key}"
    Role = each.key
  }
}
