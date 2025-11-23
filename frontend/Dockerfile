FROM node:20-slim AS build

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

# image that only contains /app/dist
FROM alpine:3.18 AS dist
WORKDIR /dist
COPY --from=build /app/dist .
