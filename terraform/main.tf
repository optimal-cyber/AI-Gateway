# =============================================================================
# Zero Trust AI Lab — root module
# =============================================================================
# AWS baseline: VPC + forced-through-proxy egress (Squid allowlist, ADR-009),
# two app EC2 hosts (SSM-only, no SSH), scoped secrets, CloudWatch logging.
# Cloudflare wiring is a stubbed module (Phase 4). NFW is an optional egress
# mode retained from ADR-004.
#
# State: local (ADR-001). fmt + validate clean; `plan` requires AWS creds.
# Do NOT apply from CI — the operator applies.
# =============================================================================

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.92" # pinned to 5.x per spec; 6.x exists (ADR notes upgrade path)
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
      Owner     = "optimal-llc"
      Env       = "lab"
    }
  }
}

# Amazon Linux 2023, x86_64 (t3 family). Public SSM parameter; resolved at plan.
data "aws_ssm_parameter" "al2023_x86_64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  al2023_ami_id = data.aws_ssm_parameter.al2023_x86_64.value
}

# -----------------------------------------------------------------------------
# Network — VPC, subnets, IGW, NAT, route tables (egress_mode-aware)
# -----------------------------------------------------------------------------
module "network" {
  source = "./modules/network"

  project_name         = var.project_name
  vpc_cidr             = var.vpc_cidr
  availability_zones   = var.availability_zones
  public_subnet_cidr   = var.public_subnet_cidr
  firewall_subnet_cidr = var.firewall_subnet_cidr
  proxy_subnet_cidr    = var.proxy_subnet_cidr
  app_subnet_cidrs     = var.app_subnet_cidrs
  egress_mode          = var.egress_mode
}

# -----------------------------------------------------------------------------
# Logging — CloudWatch log groups (created before compute so user-data can write)
# -----------------------------------------------------------------------------
module "logging" {
  source = "./modules/logging"

  project_name       = var.project_name
  log_retention_days = var.log_retention_days
}

# -----------------------------------------------------------------------------
# Secrets — 16 empty lab/* placeholders; operator seeds values post-apply
# -----------------------------------------------------------------------------
module "secrets" {
  source = "./modules/secrets"

  project_name         = var.project_name
  secret_recovery_days = var.secret_recovery_days
}

# -----------------------------------------------------------------------------
# Egress control A (default): Squid allowlist forward proxy (ADR-009)
# Only created when egress_mode == "proxy".
# -----------------------------------------------------------------------------
module "proxy" {
  source = "./modules/proxy"
  count  = var.egress_mode == "proxy" ? 1 : 0

  project_name             = var.project_name
  region                   = var.region
  ami_id                   = local.al2023_ami_id
  proxy_instance_type      = var.proxy_instance_type
  vpc_id                   = module.network.vpc_id
  proxy_subnet_id          = module.network.proxy_subnet_id
  app_subnet_cidrs         = var.app_subnet_cidrs
  egress_allowlist_domains = var.egress_allowlist_domains
  log_group_name           = module.logging.proxy_host_log_group_name
}

# -----------------------------------------------------------------------------
# Egress control B (optional): AWS Network Firewall (ADR-004, retained)
# Only created when egress_mode == "networkfirewall". Adds the app-subnet
# default route to the firewall endpoint, breaking the module cycle by owning
# that route here rather than in the network module.
# -----------------------------------------------------------------------------
module "firewall" {
  source = "./modules/firewall"
  count  = var.egress_mode == "networkfirewall" ? 1 : 0

  project_name             = var.project_name
  region                   = var.region
  vpc_id                   = module.network.vpc_id
  firewall_subnet_id       = module.network.firewall_subnet_id
  app_route_table_id       = module.network.app_route_table_id
  public_route_table_id    = module.network.public_route_table_id
  app_subnet_cidrs         = var.app_subnet_cidrs
  egress_allowlist_domains = var.egress_allowlist_domains
  log_group_name           = module.logging.networkfw_log_group_name
}

# -----------------------------------------------------------------------------
# Compute — two app hosts (chat-host, gateway-host), SSM-only
# -----------------------------------------------------------------------------
module "compute" {
  source = "./modules/compute"

  project_name      = var.project_name
  region            = var.region
  ami_id            = local.al2023_ami_id
  instance_type     = var.instance_type
  vpc_id            = module.network.vpc_id
  app_subnet_id     = module.network.app_subnet_ids[0] # single-AZ for cost (ADR-009)
  app_subnet_cidrs  = var.app_subnet_cidrs
  root_volume_gb    = var.root_volume_gb
  egress_mode       = var.egress_mode
  proxy_private_ip  = var.egress_mode == "proxy" ? module.proxy[0].proxy_private_ip : null
  no_proxy_cidr     = var.vpc_cidr
  chat_log_group    = module.logging.chat_host_log_group_name
  gateway_log_group = module.logging.gateway_host_log_group_name
}

# Populate lab/gateway_host_private_ip automatically from the compute output so
# the chat host can resolve the LiteLLM gateway without a manual seed step.
resource "aws_secretsmanager_secret_version" "gateway_host_private_ip" {
  secret_id     = module.secrets.secret_arns["gateway_host_private_ip"]
  secret_string = module.compute.gateway_host_private_ip
}

# -----------------------------------------------------------------------------
# Cloudflare — stubbed (Phase 4). See terraform/modules/cloudflare/main.tf.
# -----------------------------------------------------------------------------
# module "cloudflare" {
#   source = "./modules/cloudflare"
#   ...
# }
