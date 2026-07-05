FROM python:3.10-slim

RUN . /etc/os-release && rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list && \
    printf 'deb http://mirrors.tuna.tsinghua.edu.cn/debian/ %s main\n' "$VERSION_CODENAME" > /etc/apt/sources.list && \
    printf 'deb http://mirrors.tuna.tsinghua.edu.cn/debian/ %s-updates main\n' "$VERSION_CODENAME" >> /etc/apt/sources.list && \
    printf 'deb http://mirrors.tuna.tsinghua.edu.cn/debian-security %s-security main\n' "$VERSION_CODENAME" >> /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

RUN mkdir -p backend/upload backend/download

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]