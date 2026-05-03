.PHONY: trivy-scan

# Run Trivy filesystem scan locally (mirrors CI settings).
# Requires Trivy to be installed: https://aquasecurity.github.io/trivy/latest/getting-started/installation/
trivy-scan:
	trivy fs . \
		--severity HIGH,CRITICAL \
		--skip-dirs .venv,node_modules,dist,.output \
		--exit-code 1
