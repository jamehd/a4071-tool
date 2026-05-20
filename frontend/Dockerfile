FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY server.go ./
RUN CGO_ENABLED=0 go build -o /out/server ./

FROM alpine:3.20
COPY --from=build /out/server /server
COPY public /public
EXPOSE 4072
ENTRYPOINT ["/server"]
