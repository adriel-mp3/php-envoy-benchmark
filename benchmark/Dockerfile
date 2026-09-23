FROM golang:1.26-alpine AS build
RUN CGO_ENABLED=0 go install github.com/rakyll/hey@v0.1.4

FROM python:3.14-alpine3.23
COPY --from=build /go/bin/hey /usr/local/bin/hey
COPY report.py /benchmark/report.py
ENTRYPOINT ["python", "/benchmark/report.py"]
