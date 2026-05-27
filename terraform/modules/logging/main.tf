# =============================================================================
# logging — CloudWatch Log Groups (requirement #7)
# =============================================================================
# 30-day retention by default. EC2 hosts ship via the CloudWatch agent
# (configured in user-data). networkfw group receives flow/alert logs when
# egress_mode=networkfirewall. SSM session logs can also target these groups.
# =============================================================================

locals {
  groups = {
    chat_host    = "/ai-lab/ec2/chat-host"
    gateway_host = "/ai-lab/ec2/gateway-host"
    proxy_host   = "/ai-lab/ec2/proxy-host" # added for the Squid egress proxy (ADR-009)
    networkfw    = "/ai-lab/networkfw"
  }
}

resource "aws_cloudwatch_log_group" "this" {
  for_each          = local.groups
  name              = each.value
  retention_in_days = var.log_retention_days

  tags = { Name = "${var.project_name}-${replace(each.key, "_", "-")}" }
}
