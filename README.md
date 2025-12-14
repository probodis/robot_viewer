# Robot Telemetry Viewer

This project provides a web-based interface to view robot telemetry data synchronized with video playback. It's designed to help analyze robot performance by correlating visual events with sensor readings.

## Features

* **Data Visualization**: View robot telemetry data through interactive charts.
* **Video Playback**: Watch videos of robot operations alongside telemetry data.
* **Easy Navigation**: Switch between different orders to view their corresponding data.
 
## Project Structure

The project is divided into two main parts:

* **`backend/`**: A Python application built with **FastAPI** that serves the telemetry data and videos.
* **`frontend/`**: A JavaScript application built with **Vue.js** that provides the user interface.

## Prerequisites

Before you begin, ensure you have **Docker** and **Docker Compose** installed on your system.

## How to Run

To get the application up and running, follow these steps:

1.  **Place Your Data**

    Create a `data` directory in the project root. It must have the following structure and file naming:

    ```text
    robot_viewer/
    └── data/
        ├── orders/
        │     ├── 2025-09-04_orders.txt
        │     ├── 2025-09-05_orders.txt
        │     └── ...
        ├── start_order/
        │     ├── 2025-09-04_start_order.txt
        │     ├── 2025-09-05_start_order.txt
        │     └── ...
        └── videos/
              ├── 2024-02-11_23-47-57.mp4
              └── ...
    ```
    - All files in `orders/` must be named as `<date>_orders.txt` (for example: `2025-09-04_orders.txt`).
    - All files in `start_order/` must be named as `<date>_start_order.txt` (for example: `2025-09-04_start_order.txt`).
    - All video files in `videos/` must be named as `<date>_<time>.mp4` (for example: `2024-02-11_23-47-57.mp4`).

2.  **Start the Services**

    From the root of the project, run the following command:

    ```bash
    docker-compose up --build
    ```

    This will build the Docker images and start the frontend and backend services. The application will be running, but no data will be available yet.


3.  **No Data Processing Needed**

    You do not need to run any data preparation scripts. The backend will read the raw data files from the `orders/`, `start_order/`, and `videos/` folders directly.

4.  **Access the Application**

    * The **frontend** will be available at [http://localhost:5173/robot_viewer/](http://localhost:5173/robot_viewer/).
    * The **backend** API will be available at [http://localhost:8000/docs](http://localhost:8000/docs).

## Development
To run the application in development mode, follow these steps:

```bash
docker compose -f docker-compose.dev.yml up --build
```

## Server Deployment

1. **Nginx Configuration:**

    Add the following to your Nginx config:

    ```
    location = /robot_viewer {
        return 301 /robot_viewer/;
    }

    location /robot_viewer/ {
        alias /var/www/robot_viewer/;
        try_files $uri $uri/ /robot_viewer/index.html;
        index index.html;
    }
    ```

    This configuration makes sure that requests to `/robot_viewer` are redirected to `/robot_viewer/`, and serves the frontend files from `/var/www/robot_viewer/`.

2. **Deploy the Application:**

    Run the deployment with:

    ```bash
    make deploy
    ```
---

## Future Improvements

- [ ] **Chart Interaction Bug**: Fix the issue where the 'State' chart freezes upon mouseover.
