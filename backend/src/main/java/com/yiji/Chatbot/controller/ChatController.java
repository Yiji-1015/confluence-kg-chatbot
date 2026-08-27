package com.yiji.Chatbot.controller;

import com.yiji.Chatbot.dto.ChatMessageDto;
import com.yiji.Chatbot.dto.ChatRequestDto;
import com.yiji.Chatbot.dto.ChatResponseDto;
import com.yiji.Chatbot.dto.ChatSessionDto;
import com.yiji.Chatbot.service.ChatService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * Confluence RAG Chatbot 메인 REST API 컨트롤러
 */
@RestController
@RequestMapping("/api")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class ChatController {

    private final ChatService chatService;

    /**
     * 질문 전송 및 RAG 답변 생성 API
     * POST /api/chat
     */
    @PostMapping("/chat")
    public ResponseEntity<ChatResponseDto> chat(@Valid @RequestBody ChatRequestDto requestDto) {
        ChatResponseDto response = chatService.processChat(requestDto);
        return ResponseEntity.ok(response);
    }

    /**
     * 전체 대화방 목록 최신순 조회 API (좌측 사이드바용, userId 필터링 지원)
     * GET /api/sessions?userId={userId}
     */
    @GetMapping("/sessions")
    public ResponseEntity<List<ChatSessionDto>> getSessions(@RequestParam(name = "userId", required = false) String userId) {
        List<ChatSessionDto> sessions = chatService.getSessions(userId);
        return ResponseEntity.ok(sessions);
    }

    /**
     * 특정 대화방의 과거 전체 메시지 내역 조회 API
     * GET /api/sessions/{sessionId}/messages
     */
    @GetMapping("/sessions/{sessionId}/messages")
    public ResponseEntity<List<ChatMessageDto>> getMessages(@PathVariable("sessionId") String sessionId) {
        List<ChatMessageDto> messages = chatService.getMessages(sessionId);
        return ResponseEntity.ok(messages);
    }

    /**
     * 대화방 삭제 API
     * DELETE /api/sessions/{sessionId}
     */
    @DeleteMapping("/sessions/{sessionId}")
    public ResponseEntity<Void> deleteSession(@PathVariable("sessionId") String sessionId) {
        chatService.deleteSession(sessionId);
        return ResponseEntity.noContent().build();
    }
}
