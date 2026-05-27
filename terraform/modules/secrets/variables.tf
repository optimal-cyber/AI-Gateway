variable "project_name" {
  type = string
}

variable "secret_recovery_days" {
  description = "Secrets Manager recovery window in days (0 = force delete)."
  type        = number
}
