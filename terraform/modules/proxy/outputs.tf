output "proxy_private_ip" {
  value = aws_instance.proxy.private_ip
}

output "proxy_instance_id" {
  value = aws_instance.proxy.id
}

output "proxy_security_group_id" {
  value = aws_security_group.proxy.id
}
