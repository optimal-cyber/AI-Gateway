output "chat_host_instance_id" {
  value = aws_instance.app["chat-host"].id
}

output "gateway_host_instance_id" {
  value = aws_instance.app["gateway-host"].id
}

output "gateway_host_private_ip" {
  value = aws_instance.app["gateway-host"].private_ip
}

output "chat_host_private_ip" {
  value = aws_instance.app["chat-host"].private_ip
}

output "app_security_group_id" {
  value = aws_security_group.app.id
}

output "app_role_arn" {
  value = aws_iam_role.app.arn
}
