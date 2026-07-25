PID_FILE := var/state/app.pid
LOG_FILE := var/log/app.out

.PHONY: relaunch

relaunch:
	@mkdir -p $(dir $(PID_FILE)) $(dir $(LOG_FILE))
	@if [ -f $(PID_FILE) ] && kill -0 "$$(cat $(PID_FILE))" 2>/dev/null; then \
		pid=$$(cat $(PID_FILE)); \
		echo "stopping app (pid $$pid)"; \
		kill $$pid; \
		for i in 1 2 3 4 5 6 7 8 9 10; do \
			kill -0 $$pid 2>/dev/null || break; \
			sleep 1; \
		done; \
		kill -0 $$pid 2>/dev/null && kill -9 $$pid || true; \
	fi
	@nohup python3 app.py > $(LOG_FILE) 2>&1 & echo $$! > $(PID_FILE)
	@sleep 1
	@echo "app relaunched (pid $$(cat $(PID_FILE)))"
