# Stage 1: Build Tailwind CSS
FROM node:20-alpine AS tailwind-builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY tailwind.config.js .
COPY static/input.css ./static/input.css
COPY templates ./templates
RUN npm run build-css

# Stage 2: Python production
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=tailwind-builder /app/static/dist ./static/dist
EXPOSE 8080
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]