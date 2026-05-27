output "secret_arns" {
  description = "Map of short secret name -> ARN."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

output "secret_names" {
  description = "Map of short secret name -> full lab/* name."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.name }
}
