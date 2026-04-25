package server

import (
	"encoding/json"
	"net/http"
)

type anthropicError struct {
	Type  string `json:"type"`
	Error struct {
		Type    string `json:"type"`
		Message string `json:"message"`
	} `json:"error"`
}

type openaiError struct {
	Error struct {
		Message string `json:"message"`
		Type    string `json:"type"`
		Code    string `json:"code"`
	} `json:"error"`
}

func WriteError(w http.ResponseWriter, protocol string, statusCode int, errorType string, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)

	if protocol == "anthropic" {
		resp := anthropicError{Type: "error"}
		resp.Error.Type = errorType
		resp.Error.Message = message
		json.NewEncoder(w).Encode(resp)
	} else {
		resp := openaiError{}
		resp.Error.Message = message
		resp.Error.Type = errorType
		resp.Error.Code = errorType
		json.NewEncoder(w).Encode(resp)
	}
}
