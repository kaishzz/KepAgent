package constants

var SupportedCommands = []string{
	"agent.ping",
	"docker.list_servers",
	"docker.start_server",
	"docker.stop_server",
	"docker.restart_server",
	"docker.remove_server",
	"docker.start_group",
	"docker.stop_group",
	"docker.restart_group",
	"node.kill_all",
	"node.rcon_command",
	"node.check_update",
	"node.check_validate",
	"node.get_local_build",
	"node.get_remote_build",
	"node.monitor_check",
	"node.monitor_start",
	"node.replay_list",
	"node.replay_import",
	"node.replay_export",
}
