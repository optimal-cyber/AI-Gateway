variable "project_name" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "availability_zones" {
  type = list(string)
}

variable "public_subnet_cidr" {
  type = string
}

variable "firewall_subnet_cidr" {
  type = string
}

variable "proxy_subnet_cidr" {
  type = string
}

variable "app_subnet_cidrs" {
  type = list(string)
}

variable "egress_mode" {
  type = string
}
