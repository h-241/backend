output "app_url" {
  value = aws_elastic_beanstalk_environment.app_env.cname
}