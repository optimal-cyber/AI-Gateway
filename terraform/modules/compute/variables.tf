variable "project_name" {
  type = string
}

variable "region" {
  type = string
}

variable "ami_id" {
  type = string
}

variable "instance_type" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "app_subnet_id" {
  description = "Single app subnet (azs[0]) — both hosts here for single-AZ cost."
  type        = string
}

variable "app_subnet_cidrs" {
  description = "App subnet CIDRs, for the intra-subnet 4000 ingress (chat -> gateway)."
  type        = list(string)
}

variable "root_volume_gb" {
  type = number
}

variable "egress_mode" {
  type = string
}

variable "proxy_private_ip" {
  description = "Squid proxy IP for HTTP(S)_PROXY. Null/empty in networkfirewall mode (egress is routed, not proxied)."
  type        = string
  default     = null
}

variable "no_proxy_cidr" {
  description = "VPC CIDR added to NO_PROXY so intra-VPC traffic skips the proxy."
  type        = string
}

variable "chat_log_group" {
  type = string
}

variable "gateway_log_group" {
  type = string
}
