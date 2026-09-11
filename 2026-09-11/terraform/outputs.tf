output "alb_dns_name" {
  value       = aws_lb.app.dns_name
  description = "Public URL: http://<this>/healthz and http://<this>/generate"
}

output "ecr_repository_url" {
  value       = aws_ecr_repository.app.repository_url
  description = "Push the built image here before applying (or before the service can start successfully)"
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.app.name
}

output "ecs_service_name" {
  value = aws_ecs_service.app.name
}
