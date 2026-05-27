output "chat_host_log_group_name" {
  value = aws_cloudwatch_log_group.this["chat_host"].name
}

output "gateway_host_log_group_name" {
  value = aws_cloudwatch_log_group.this["gateway_host"].name
}

output "proxy_host_log_group_name" {
  value = aws_cloudwatch_log_group.this["proxy_host"].name
}

output "networkfw_log_group_name" {
  value = aws_cloudwatch_log_group.this["networkfw"].name
}

output "log_group_arns" {
  description = "All lab log group ARNs (for scoping instance-role CloudWatch permissions)."
  value       = [for g in aws_cloudwatch_log_group.this : g.arn]
}
