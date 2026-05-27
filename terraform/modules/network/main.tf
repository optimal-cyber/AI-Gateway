# =============================================================================
# network — VPC, subnets, IGW, NAT, route tables
# =============================================================================
# Topology (single active AZ = azs[0] for cost):
#
#   public  (azs[0])  10.50.0.0/24   NAT Gateway + IGW route
#   firewall(azs[0])  10.50.1.0/24   NFW endpoint  (only used in networkfirewall mode)
#   proxy   (azs[0])  10.50.2.0/24   Squid         (only used in proxy mode) -> NAT
#   app     (azs[0])  10.50.10.0/24  EC2 hosts     -- NO 0.0.0.0/0 route
#   app     (azs[1])  10.50.11.0/24  spare (future HA)
#
# The app route table has NO default route. The only path to the internet is the
# Squid proxy's private IP (reached via the in-VPC `local` route). This is the
# load-bearing egress invariant (ADR-009): non-proxy-aware traffic simply cannot
# leave. In networkfirewall mode the firewall module adds the app default route.
# =============================================================================

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.project_name}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.project_name}-igw" }
}

# ---- subnets ----------------------------------------------------------------
resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.public_subnet_cidr
  availability_zone = var.availability_zones[0]
  tags              = { Name = "${var.project_name}-public", Tier = "public" }
}

resource "aws_subnet" "firewall" {
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.firewall_subnet_cidr
  availability_zone = var.availability_zones[0]
  tags              = { Name = "${var.project_name}-firewall", Tier = "firewall" }
}

resource "aws_subnet" "proxy" {
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.proxy_subnet_cidr
  availability_zone = var.availability_zones[0]
  tags              = { Name = "${var.project_name}-proxy", Tier = "egress-proxy" }
}

resource "aws_subnet" "app" {
  count             = length(var.app_subnet_cidrs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.app_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]
  tags              = { Name = "${var.project_name}-app-${count.index}", Tier = "app" }
}

# ---- NAT --------------------------------------------------------------------
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${var.project_name}-nat-eip" }
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id
  tags          = { Name = "${var.project_name}-nat" }

  depends_on = [aws_internet_gateway.this]
}

# ---- route tables -----------------------------------------------------------
# public: out via IGW
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.project_name}-rt-public" }
}

resource "aws_route" "public_default" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# egress-tier (proxy + firewall subnets): out via NAT
resource "aws_route_table" "egress" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.project_name}-rt-egress" }
}

resource "aws_route" "egress_default" {
  route_table_id         = aws_route_table.egress.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this.id
}

resource "aws_route_table_association" "proxy" {
  subnet_id      = aws_subnet.proxy.id
  route_table_id = aws_route_table.egress.id
}

resource "aws_route_table_association" "firewall" {
  subnet_id      = aws_subnet.firewall.id
  route_table_id = aws_route_table.egress.id
}

# app route table. In proxy mode it gets a default route to NAT, but the app
# security group blocks direct 80/443 egress — so HTTP/HTTPS can ONLY leave via
# the Squid proxy (3128), while cloudflared's tunnel (udp/tcp 7844) is the one
# protocol allowed straight out (cloudflared cannot use an HTTP proxy; ADR-009).
# In networkfirewall mode the firewall module instead adds 0.0.0.0/0 -> endpoint.
resource "aws_route_table" "app" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.project_name}-rt-app" }
}

resource "aws_route" "app_default" {
  count                  = var.egress_mode == "proxy" ? 1 : 0
  route_table_id         = aws_route_table.app.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this.id
}

resource "aws_route_table_association" "app" {
  count          = length(aws_subnet.app)
  subnet_id      = aws_subnet.app[count.index].id
  route_table_id = aws_route_table.app.id
}
