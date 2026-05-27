variable "project_name" {
  type = string
}

variable "region" {
  type = string
}

variable "ami_id" {
  type = string
}

variable "proxy_instance_type" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "proxy_subnet_id" {
  type = string
}

variable "app_subnet_cidrs" {
  description = "Allowed source CIDRs for inbound 3128 (the app subnets)."
  type        = list(string)
}

variable "egress_allowlist_domains" {
  description = "Third-party allowlist. AWS service endpoints are appended from region."
  type        = list(string)
}

variable "log_group_name" {
  description = "CloudWatch log group for squid access.log."
  type        = string
}
