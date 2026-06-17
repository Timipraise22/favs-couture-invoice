# Start with a lightweight Python environment
FROM python:3.10-slim-bullseye

# Install the wkhtmltopdf engine
RUN apt-get update && apt-get install -y wkhtmltopdf

# Set up the app directory
WORKDIR /app

# Install the Python libraries
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your files into the cloud computer
COPY . .

# Run the app using Gunicorn
CMD exec gunicorn --bind 0.0.0.0:$PORT app:app