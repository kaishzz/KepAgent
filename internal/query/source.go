package query

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"net"
	"strconv"
	"time"
)

var a2sInfoRequest = []byte{0xff, 0xff, 0xff, 0xff, 0x54, 'S', 'o', 'u', 'r', 'c', 'e', ' ', 'E', 'n', 'g', 'i', 'n', 'e', ' ', 'Q', 'u', 'e', 'r', 'y', 0x00}

func Info(host string, port int, timeout time.Duration) (map[string]any, error) {
	addr := net.JoinHostPort(host, strconv.Itoa(port))
	conn, err := net.DialTimeout("udp", addr, timeout)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(timeout))

	if _, err := conn.Write(a2sInfoRequest); err != nil {
		return nil, err
	}
	buf := make([]byte, 4096)
	n, err := conn.Read(buf)
	if err != nil {
		return nil, err
	}
	payload := buf[:n]
	if len(payload) >= 9 && payload[4] == 0x41 {
		challenge := payload[5:9]
		request := append([]byte{}, a2sInfoRequest...)
		request = append(request, challenge...)
		if _, err := conn.Write(request); err != nil {
			return nil, err
		}
		n, err = conn.Read(buf)
		if err != nil {
			return nil, err
		}
		payload = buf[:n]
	}
	return parseInfo(payload)
}

func parseInfo(payload []byte) (map[string]any, error) {
	if len(payload) < 6 || !bytes.Equal(payload[:4], []byte{0xff, 0xff, 0xff, 0xff}) || payload[4] != 0x49 {
		return nil, fmt.Errorf("invalid A2S_INFO response")
	}
	offset := 6
	name, offset := readCString(payload, offset)
	mapName, offset := readCString(payload, offset)
	folder, offset := readCString(payload, offset)
	game, offset := readCString(payload, offset)
	if offset+2+5 > len(payload) {
		return nil, fmt.Errorf("truncated A2S_INFO response")
	}
	appID := int(binary.LittleEndian.Uint16(payload[offset : offset+2]))
	offset += 2
	players := int(payload[offset])
	maxPlayers := int(payload[offset+1])
	bots := int(payload[offset+2])
	if players >= bots {
		players -= bots
	} else {
		players = 0
	}
	serverType := string(payload[offset+3])
	environment := string(payload[offset+4])
	visibility := "public"
	if offset+5 < len(payload) && payload[offset+5] == 1 {
		visibility = "private"
	}
	return map[string]any{
		"serverName":  name,
		"map":         mapName,
		"folder":      folder,
		"game":        game,
		"appId":       appID,
		"online":      players,
		"capacity":    maxPlayers,
		"serverType":  serverType,
		"environment": environment,
		"visibility":  visibility,
	}, nil
}

func readCString(payload []byte, offset int) (string, int) {
	end := bytes.IndexByte(payload[offset:], 0)
	if end < 0 {
		return "", len(payload)
	}
	return string(payload[offset : offset+end]), offset + end + 1
}
