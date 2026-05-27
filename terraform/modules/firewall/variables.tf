variable "project_name" {
  type = string
}

variable "region" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "firewall_subnet_id" {
  type = string
}

variable "app_route_table_id" {
  description = "App route table — this module owns the 0.0.0.0/0 -> firewall endpoint route."
  type        = string
}

variable "public_route_table_id" {
  description = "Public route table — return route (app CIDR -> firewall endpoint) for symmetric inspection."
  type        = string
}

variable "app_subnet_cidrs" {
  type = list(string)
}

variable "egress_allowlist_domains" {
  type = list(string)
}

variable "log_group_name" {
  type = string
}
