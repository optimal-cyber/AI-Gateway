output "vpc_id" {
  description = "Lab VPC ID."
  value       = module.network.vpc_id
}

output "egress_mode" {
  description = "Active egress control."
  value       = var.egress_mode
}

output "proxy_private_ip" {
  description = "Squid proxy private IP (null unless egress_mode=proxy). App hosts use this as HTTP(S)_PROXY:3128."
  value       = var.egress_mode == "proxy" ? module.proxy[0].proxy_private_ip : null
}

output "chat_host_instance_id" {
  description = "Instance ID for SSM: aws ssm start-session --target <id>"
  value       = module.compute.chat_host_instance_id
}

output "gateway_host_instance_id" {
  description = "Instance ID for SSM: aws ssm start-session --target <id>"
  value       = module.compute.gateway_host_instance_id
}

output "gateway_host_private_ip" {
  description = "LiteLLM gateway private IP (also written to lab/gateway_host_private_ip)."
  value       = module.compute.gateway_host_private_ip
}

output "secret_arns" {
  description = "Map of lab/* secret name -> ARN, for the seed scripts."
  value       = module.secrets.secret_arns
}

output "ssm_session_hint" {
  description = "How to open an operator shell (no SSH; ADR-006)."
  value       = "aws ssm start-session --region ${var.region} --target ${module.compute.gateway_host_instance_id}"
}
