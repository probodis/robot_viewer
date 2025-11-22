update-dev:
	git fetch origin
	git reset --hard origin/main
	docker compose -f docker-compose.yml up --build -d