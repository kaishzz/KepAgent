package rcon

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"time"
)

const (
	packetAuth     int32 = 3
	packetCommand  int32 = 2
	packetResponse int32 = 0
)

func Run(host string, port int, password, command string, timeout time.Duration) (string, error) {
	conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), timeout)
	if err != nil {
		return "", err
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(timeout))

	if err := writePacket(conn, 1, packetAuth, password); err != nil {
		return "", err
	}
	authOK := false
	for i := 0; i < 2; i++ {
		id, _, _, err := readPacket(conn)
		if err != nil {
			return "", err
		}
		if id == 1 {
			authOK = true
			break
		}
		if id == -1 {
			return "", fmt.Errorf("RCON authentication failed")
		}
	}
	if !authOK {
		return "", fmt.Errorf("RCON authentication failed")
	}

	if err := writePacket(conn, 2, packetCommand, command); err != nil {
		return "", err
	}
	id, _, body, err := readPacket(conn)
	if err != nil {
		return "", err
	}
	if id != 2 {
		return "", fmt.Errorf("unexpected RCON response id %d", id)
	}
	return strings.TrimSpace(body), nil
}

func writePacket(w io.Writer, id int32, packetType int32, body string) error {
	var payload bytes.Buffer
	_ = binary.Write(&payload, binary.LittleEndian, id)
	_ = binary.Write(&payload, binary.LittleEndian, packetType)
	payload.WriteString(body)
	payload.WriteByte(0)
	payload.WriteByte(0)
	size := int32(payload.Len())
	if err := binary.Write(w, binary.LittleEndian, size); err != nil {
		return err
	}
	_, err := w.Write(payload.Bytes())
	return err
}

func readPacket(r io.Reader) (int32, int32, string, error) {
	var size int32
	if err := binary.Read(r, binary.LittleEndian, &size); err != nil {
		return 0, 0, "", err
	}
	if size < 10 || size > 1024*1024 {
		return 0, 0, "", fmt.Errorf("invalid RCON packet size %d", size)
	}
	payload := make([]byte, size)
	if _, err := io.ReadFull(r, payload); err != nil {
		return 0, 0, "", err
	}
	id := int32(binary.LittleEndian.Uint32(payload[0:4]))
	packetType := int32(binary.LittleEndian.Uint32(payload[4:8]))
	body := string(bytes.TrimRight(payload[8:], "\x00"))
	_ = packetResponse
	return id, packetType, body, nil
}
