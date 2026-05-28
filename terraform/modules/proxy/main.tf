# =============================================================================
# proxy — hardened Squid forward proxy (default egress control, ADR-009)
# =============================================================================
# Enforces a dstdomain allowlist with a default-deny tail. No public IP; SG
# accepts 3128 only from the app subnets; caching disabled (no traffic at rest).
# App hosts have no other route to the internet, so this is the sole egress path.
# =============================================================================

locals {
  # Regional AWS service endpoints the app/proxy hosts must reach THROUGH the
  # proxy (SSM Session Manager, Secrets Manager, CloudWatch Logs, S3). Listed
  # narrowly rather than a blanket .amazonaws.com to limit exfil surface.
  aws_service_domains = [
    "ssm.${var.region}.amazonaws.com",
    "ssmmessages.${var.region}.amazonaws.com",
    "ec2messages.${var.region}.amazonaws.com",
    "secretsmanager.${var.region}.amazonaws.com",
    "logs.${var.region}.amazonaws.com",
    # Leading-dot covers BOTH the apex (s3.<region>.amazonaws.com) AND virtual-
    # hosted buckets. Listing both forms makes Squid bail with "subdomain of"
    # / "Bungled" and refuse to start. One s3 entry per form, leading-dot only.
    ".s3.${var.region}.amazonaws.com",
    ".s3.dualstack.${var.region}.amazonaws.com",
  ]

  merged_allowlist = concat(var.egress_allowlist_domains, local.aws_service_domains)
}

# ---- security group ---------------------------------------------------------
resource "aws_security_group" "proxy" {
  name        = "${var.project_name}-proxy-sg"
  description = "Squid egress proxy: 3128 inbound from app subnets only; 80/443/53 outbound."
  vpc_id      = var.vpc_id

  tags = { Name = "${var.project_name}-proxy-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "proxy_3128" {
  count             = length(var.app_subnet_cidrs)
  security_group_id = aws_security_group.proxy.id
  description       = "Squid proxy port from app subnet ${count.index}"
  cidr_ipv4         = var.app_subnet_cidrs[count.index]
  from_port         = 3128
  to_port           = 3128
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "proxy_https" {
  security_group_id = aws_security_group.proxy.id
  description       = "Outbound HTTPS to allowed domains (enforced by squid app logic)"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "proxy_http" {
  security_group_id = aws_security_group.proxy.id
  description       = "Outbound HTTP to allowed package/OS mirrors"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "proxy_dns_udp" {
  security_group_id = aws_security_group.proxy.id
  description       = "DNS to VPC resolver"
  cidr_ipv4         = data.aws_vpc.this.cidr_block
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
}

resource "aws_vpc_security_group_egress_rule" "proxy_dns_tcp" {
  security_group_id = aws_security_group.proxy.id
  description       = "DNS (TCP) to VPC resolver"
  cidr_ipv4         = data.aws_vpc.this.cidr_block
  from_port         = 53
  to_port           = 53
  ip_protocol       = "tcp"
}

data "aws_vpc" "this" {
  id = var.vpc_id
}

# ---- IAM (SSM + CloudWatch logs only) ---------------------------------------
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

resource "aws_iam_role" "proxy" {
  name               = "${var.project_name}-proxy-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = { Name = "${var.project_name}-proxy-role" }
}

# Managed policy gives the SSM agent what it needs for Session Manager.
resource "aws_iam_role_policy_attachment" "proxy_ssm" {
  role       = aws_iam_role.proxy.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "proxy_logs" {
  statement {
    sid     = "ShipSquidLogs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:${var.log_group_name}",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:${var.log_group_name}:*",
    ]
  }
}

resource "aws_iam_role_policy" "proxy_logs" {
  name   = "${var.project_name}-proxy-logs"
  role   = aws_iam_role.proxy.id
  policy = data.aws_iam_policy_document.proxy_logs.json
}

resource "aws_iam_instance_profile" "proxy" {
  name = "${var.project_name}-proxy-profile"
  role = aws_iam_role.proxy.name
}

# ---- instance ---------------------------------------------------------------
resource "aws_instance" "proxy" {
  ami                         = var.ami_id
  instance_type               = var.proxy_instance_type
  subnet_id                   = var.proxy_subnet_id
  vpc_security_group_ids      = [aws_security_group.proxy.id]
  iam_instance_profile        = aws_iam_instance_profile.proxy.name
  associate_public_ip_address = false

  # No SSH key (ADR-006). IMDSv2 required (T-AWS-S).
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    encrypted   = true
  }

  user_data = templatefile("${path.module}/proxy-user-data.sh.tftpl", {
    allowlist      = join("\n", local.merged_allowlist)
    log_group_name = var.log_group_name
    region         = var.region
  })

  tags = { Name = "${var.project_name}-proxy" }
}
