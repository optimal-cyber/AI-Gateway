output "firewall_arn" {
  value = aws_networkfirewall_firewall.this.arn
}

output "firewall_endpoint_id" {
  value = local.fw_endpoint_id
}

output "rule_group_arn" {
  value = aws_networkfirewall_rule_group.allowlist.arn
}
