update-dev:
	git fetch origin
	git reset --hard origin/main
	docker compose down
	docker compose -f docker-compose.yml up --build -d