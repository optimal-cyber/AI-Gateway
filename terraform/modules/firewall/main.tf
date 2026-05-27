# =============================================================================
# firewall — AWS Network Firewall egress allowlist (optional; ADR-004 retained)
# =============================================================================
# Active only when egress_mode = "networkfirewall". A stateful domain-list
# rule group of type ALLOWLIST (HTTP_HOST + TLS_SNI) passes the listed domains
# and drops all other HTTP/TLS egress by default (AWS-documented behavior). The
# firewall endpoint sits in a dedicated subnet; this module owns the routes that
# steer app traffic through it (kept here to avoid a network<->firewall cycle).
#
# Cost note: ~$0.395/endpoint-hour (~$288/mo). See ADR-009 for why this is the
# optional path, not the default.
# =============================================================================

locals {
  aws_service_domains = [
    "ssm.${var.region}.amazonaws.com",
    "ssmmessages.${var.region}.amazonaws.com",
    "ec2messages.${var.region}.amazonaws.com",
    "secretsmanager.${var.region}.amazonaws.com",
    "logs.${var.region}.amazonaws.com",
    "s3.${var.region}.amazonaws.com",
    ".s3.${var.region}.amazonaws.com",
    ".s3.dualstack.${var.region}.amazonaws.com",
  ]
  merged_allowlist = concat(var.egress_allowlist_domains, local.aws_service_domains)

  # One firewall endpoint (single AZ). Pull its VPCE id from sync state.
  fw_endpoint_id = one([
    for ss in aws_networkfirewall_firewall.this.firewall_status[0].sync_states :
    ss.attachment[0].endpoint_id
  ])
}

resource "aws_networkfirewall_rule_group" "allowlist" {
  name     = "${var.project_name}-egress-allowlist"
  type     = "STATEFUL"
  capacity = 200

  rule_group {
    rules_source {
      rules_source_list {
        generated_rules_type = "ALLOWLIST"
        target_types         = ["HTTP_HOST", "TLS_SNI"]
        targets              = local.merged_allowlist
      }
    }
  }

  tags = { Name = "${var.project_name}-egress-allowlist" }
}

resource "aws_networkfirewall_firewall_policy" "this" {
  name = "${var.project_name}-egress-policy"

  firewall_policy {
    stateless_default_actions          = ["aws:forward_to_sfe"]
    stateless_fragment_default_actions = ["aws:forward_to_sfe"]

    stateful_rule_group_reference {
      resource_arn = aws_networkfirewall_rule_group.allowlist.arn
    }
  }

  tags = { Name = "${var.project_name}-egress-policy" }
}

resource "aws_networkfirewall_firewall" "this" {
  name                = "${var.project_name}-fw"
  firewall_policy_arn = aws_networkfirewall_firewall_policy.this.arn
  vpc_id              = var.vpc_id

  subnet_mapping {
    subnet_id = var.firewall_subnet_id
  }

  tags = { Name = "${var.project_name}-fw" }
}

resource "aws_networkfirewall_logging_configuration" "this" {
  firewall_arn = aws_networkfirewall_firewall.this.arn

  logging_configuration {
    log_destination_config {
      log_destination      = { logGroup = var.log_group_name }
      log_destination_type = "CloudWatchLogs"
      log_type             = "ALERT"
    }
    log_destination_config {
      log_destination      = { logGroup = var.log_group_name }
      log_destination_type = "CloudWatchLogs"
      log_type             = "FLOW"
    }
  }
}

# ---- routing: steer app traffic through the firewall endpoint ---------------
resource "aws_route" "app_to_firewall" {
  route_table_id         = var.app_route_table_id
  destination_cidr_block = "0.0.0.0/0"
  vpc_endpoint_id        = local.fw_endpoint_id
}

# return path: NAT replies to app subnets must re-enter the firewall (symmetry)
resource "aws_route" "return_to_firewall" {
  count                  = length(var.app_subnet_cidrs)
  route_table_id         = var.public_route_table_id
  destination_cidr_block = var.app_subnet_cidrs[count.index]
  vpc_endpoint_id        = local.fw_endpoint_id
}
