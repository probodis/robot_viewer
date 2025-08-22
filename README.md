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

    Create a `data` directory in the project root. It must have the following structure:

    ```text
    robot_viewer/
    └── data/
        ├── orders_logs.txt
        ├── telemetry_logs.txt
        └── videos/
            ├── 2024-02-11_23-47-57.mp4
            └── ...
        └── processed_data/  <- This directory will be created by the script
    ```
    *The `processed_data` directory will be created automatically when you run the processing script.*

2.  **Start the Services**

    From the root of the project, run the following command:

    ```bash
    docker-compose up --build
    ```

    This will build the Docker images and start the frontend and backend services. The application will be running, but no data will be available yet.

3.  **Process the Data**

    In a **new terminal window**, run the following command to execute the data preparation script inside the running `backend` container:

    ```bash
    docker-compose exec backend python scripts/prepare_data.py
    ```
    
    This script processes files from the mounted `/data` volume and saves the results into `./data/processed_data/`. The API will automatically detect and load the new data on the next request.

4.  **Access the Application**

    * The **frontend** will be available at [http://localhost:5173](http://localhost:5173).
    * The **backend** API will be available at [http://localhost:8000/docs](http://localhost:8000/docs).

---

## Future Improvements

- [ ] **Web UI for Preprocessing**: Create a user interface for running the data preparation script to avoid manual command-line execution.
- [ ] **Chart Interaction Bug**: Fix the issue where the 'State' chart freezes upon mouseover.
- [ ] **Security Enhancement**: Replace the use of `ast.literal_eval` in `prepare_data.py` with a safer data parsing method (e.g., JSON) to mitigate potential security risks.
