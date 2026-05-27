variable "project_name" {
  description = "Name prefix / tag for all lab resources."
  type        = string
  default     = "ai-lab"
}

variable "region" {
  description = "AWS region. Egress allowlist AWS-service domains are derived from this."
  type        = string
  default     = "us-east-1"
}

variable "availability_zones" {
  description = "Two AZs. Compute runs single-AZ (azs[0]) for cost; azs[1] holds a spare app subnet for future HA."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]

  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "Provide exactly two availability zones."
  }
}

variable "vpc_cidr" {
  description = "VPC CIDR. Also used as NO_PROXY so intra-VPC traffic skips the proxy."
  type        = string
  default     = "10.50.0.0/16"
}

variable "public_subnet_cidr" {
  description = "Public subnet (NAT Gateway + IGW route). az[0]."
  type        = string
  default     = "10.50.0.0/24"
}

variable "firewall_subnet_cidr" {
  description = "Dedicated subnet for the AWS Network Firewall endpoint (only used when egress_mode=networkfirewall). az[0]."
  type        = string
  default     = "10.50.1.0/24"
}

variable "proxy_subnet_cidr" {
  description = "Subnet for the Squid egress proxy (only used when egress_mode=proxy). Routes to NAT. az[0]."
  type        = string
  default     = "10.50.2.0/24"
}

variable "app_subnet_cidrs" {
  description = "App subnets, one per AZ. No 0.0.0.0/0 route — egress is forced through the proxy/firewall."
  type        = list(string)
  default     = ["10.50.10.0/24", "10.50.11.0/24"]

  validation {
    condition     = length(var.app_subnet_cidrs) == 2
    error_message = "Provide exactly two app subnet CIDRs (one per AZ)."
  }
}

variable "egress_mode" {
  description = "Egress control: 'proxy' (Squid allowlist, default, ~$41/mo) or 'networkfirewall' (AWS NFW, ~$288/mo). Mutually exclusive. See ADR-009."
  type        = string
  default     = "proxy"

  validation {
    condition     = contains(["proxy", "networkfirewall"], var.egress_mode)
    error_message = "egress_mode must be 'proxy' or 'networkfirewall'."
  }
}

variable "instance_type" {
  description = "App host instance type (chat-host, gateway-host)."
  type        = string
  default     = "t3.small"
}

variable "proxy_instance_type" {
  description = "Squid proxy instance type."
  type        = string
  default     = "t3.micro"
}

variable "root_volume_gb" {
  description = "Root gp3 EBS size (GB) for app hosts. Open WebUI + Postgres + container images need headroom."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for all lab log groups."
  type        = number
  default     = 30
}

variable "secret_recovery_days" {
  description = "Secrets Manager recovery window. 0 = immediate delete (convenient for a lab that is torn down/recreated)."
  type        = number
  default     = 7
}

variable "egress_allowlist_domains" {
  description = <<-EOT
    Third-party egress allowlist (provider APIs, container registries, package
    mirrors, Cloudflare tunnel endpoints). Leading-dot form matches the domain and
    all subdomains (Squid dstdomain + NFW domain-list convention). Regional AWS
    service endpoints (ssm/ssmmessages/ec2messages/secretsmanager/logs/s3) are
    appended automatically by the egress module from var.region — do not list them
    here. See ADR-009.
  EOT
  type        = list(string)
  default = [
    # --- LLM providers ---
    ".openai.com",    # api.openai.com
    ".anthropic.com", # api.anthropic.com
    # --- government data (compliance MCP) ---
    ".sam.gov", # api.sam.gov
    # --- container registries ---
    ".ghcr.io",               # GitHub Container Registry (Open WebUI, LiteLLM images)
    ".github.com",            # cloudflared + docker-compose plugin release downloads
    ".githubusercontent.com", # ghcr blobs + release assets: pkg-containers / objects.githubusercontent.com
    ".docker.io",             # registry-1.docker.io, auth.docker.io (postgres:15-alpine)
    ".docker.com",            # production.cloudflare.docker.com (docker layer blobs)
    # --- python package mirrors (MCP/NeMo image builds) ---
    ".pypi.org",
    ".pythonhosted.org", # files.pythonhosted.org
    # --- OS package mirrors ---
    ".amazonlinux.com", # AL2023 dnf: cdn.amazonlinux.com
    ".ubuntu.com",      # security/archive.ubuntu.com (per spec; for ubuntu-based images)
    # --- Cloudflare Zero Trust (cloudflared on the hosts) ---
    ".cloudflareaccess.com",
    ".argotunnel.com",
    ".cloudflare.com", # api.cloudflare.com, cloudflared update channel
  ]
}
