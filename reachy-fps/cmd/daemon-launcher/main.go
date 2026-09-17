// daemon-launcher is the executable inside "Reachy Mini Daemon.app".
//
// macOS grants camera and microphone access per app. A daemon started from
// tmux has no owning app, so macOS denies access without ever prompting. When
// this launcher runs as an app (via `open`), it owns the permission and the
// daemon it spawns inherits it.
package main

import (
	"flag"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"syscall"
)

func main() {
	daemon := flag.String("daemon", "", "path to the reachy-mini-daemon executable")
	logPath := flag.String("log", "", "file to append daemon output to")
	flag.Parse()
	if *daemon == "" {
		log.Fatal("-daemon is required")
	}

	out := os.Stdout
	if *logPath != "" {
		f, err := os.OpenFile(*logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
		if err != nil {
			log.Fatal(err)
		}
		defer f.Close()
		out = f
		log.SetOutput(f)
	}

	cmd := exec.Command(*daemon, flag.Args()...)
	cmd.Stdout = out
	cmd.Stderr = out
	if err := cmd.Start(); err != nil {
		log.Fatalf("start daemon: %v", err)
	}
	log.Printf("launcher: daemon started (pid %d)", cmd.Process.Pid)

	// Forward stop signals so the daemon can park the robot and release
	// the camera cleanly.
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP)
	go func() {
		for s := range sigs {
			cmd.Process.Signal(syscall.SIGINT)
			log.Printf("launcher: forwarded %v to daemon", s)
		}
	}()

	err := cmd.Wait()
	log.Printf("launcher: daemon exited: %v", err)
	if exitErr, ok := err.(*exec.ExitError); ok {
		os.Exit(exitErr.ExitCode())
	}
}
