# The registry sandbox images are pushed to and pulled from. On Docker Desktop the host network is the VM's:
# its Docker pulls from localhost:5055, and containers push to the bridge gateway, 172.17.0.1:5055.
job "gradeflow-registry" {
  type = "service"

  group "registry" {
    ephemeral_disk {
      sticky = true
      size   = 4000
    }

    task "registry" {
      driver = "docker"

      config {
        image        = "registry:2"
        network_mode = "host"
      }

      env {
        REGISTRY_HTTP_ADDR                        = "0.0.0.0:5055"
        REGISTRY_STORAGE_FILESYSTEM_ROOTDIRECTORY = "/alloc/data"
      }

      resources {
        cpu    = 200
        memory = 256
      }
    }
  }
}
