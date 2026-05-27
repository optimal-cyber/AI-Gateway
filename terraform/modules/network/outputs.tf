output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_id" {
  value = aws_subnet.public.id
}

output "firewall_subnet_id" {
  value = aws_subnet.firewall.id
}

output "proxy_subnet_id" {
  value = aws_subnet.proxy.id
}

output "app_subnet_ids" {
  value = aws_subnet.app[*].id
}

output "app_route_table_id" {
  description = "App route table — firewall module adds the default route here in networkfirewall mode."
  value       = aws_route_table.app.id
}

output "public_route_table_id" {
  description = "Public route table — firewall module adds the return route here for symmetric inspection."
  value       = aws_route_table.public.id
}

output "nat_gateway_id" {
  value = aws_nat_gateway.this.id
}
