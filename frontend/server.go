package main

import (
	"log"
	"net/http"
	"os"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "4072"
	}
	fs := http.FileServer(http.Dir("/public"))
	mux := http.NewServeMux()
	mux.Handle("/", fs)
	log.Printf("frontend serving /public on :%s", port)
	if err := http.ListenAndServe(":"+port, mux); err != nil {
		log.Fatal(err)
	}
}
