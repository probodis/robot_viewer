FRONTEND_DIR=frontend
TARGET_DIR=/var/www/robot_viewer

update-dev:
	git fetch origin
	git reset --hard origin/main

deploy-frontend:
	@echo ">>> Building frontend in Docker..."
	docker build -f $(FRONTEND_DIR)/Dockerfile.build -t robot-viewer-build $(FRONTEND_DIR)

	@echo ">>> Copying built files..."
	docker run --rm \
		-v $(TARGET_DIR):/dist \
		robot-viewer-build \
		bash -c "rm -rf /dist/* && cp -r /app/dist/* /dist/"

	@echo ">>> Reloading nginx..."
	sudo systemctl reload nginx

	@echo ">>> Deployment finished!"

deploy-backend:
	@echo ">>> Deploying backend..."
	docker compose -f docker-compose.yml up -d --build backend
	@echo ">>> Backend deployment finished!"

deploy: update-dev deploy-frontend deploy-backend