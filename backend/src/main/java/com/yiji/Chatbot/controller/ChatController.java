package com.yiji.Chatbot.controller;

import com.yiji.Chatbot.dto.ChatMessageDto;
import com.yiji.Chatbot.dto.ChatRequestDto;
import com.yiji.Chatbot.dto.ChatResponseDto;
import com.yiji.Chatbot.dto.ChatSessionDto;
import com.yiji.Chatbot.service.ChatService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * Confluence RAG Chatbot 메인 REST API 컨트롤러
 *
 * 프론트엔드는 같은 8080에서 resources/static/index.html로 서빙되므로 동일 출처다.
 * CORS 설정은 두지 않는다. 프론트를 분리하게 되면 컨트롤러가 아니라
 * WebMvcConfigurer#addCorsMappings에서 설정값으로 받는다.
 */
@RestController
@RequestMapping("/api")
@RequiredArgsConstructor
public class ChatController {

    private final ChatService chatService;

    /**
     * 질문 전송 및 RAG 답변 생성
     * POST /api/chat
     */
    @PostMapping("/chat")
    public ChatResponseDto chat(@Valid @RequestBody ChatRequestDto requestDto) {
        return chatService.processChat(requestDto);
    }

    /**
     * 전체 대화방 목록 최신순 조회 (좌측 사이드바용)
     * GET /api/sessions?userId={userId}
     */
    @GetMapping("/sessions")
    public List<ChatSessionDto> getSessions(@RequestParam(name = "userId") String userId) {
        return chatService.getSessions(userId);
    }

    /**
     * 특정 대화방의 과거 전체 메시지 내역 조회
     * GET /api/sessions/{sessionId}/messages?userId={userId}
     */
    @GetMapping("/sessions/{sessionId}/messages")
    public List<ChatMessageDto> getMessages(
            @PathVariable("sessionId") String sessionId,
            @RequestParam(name = "userId") String userId) {
        return chatService.getMessages(sessionId, userId);
    }

    /**
     * 대화방 삭제
     * DELETE /api/sessions/{sessionId}?userId={userId}
     *
     * 본문 없이 204를 주므로 ResponseEntity를 쓴다. 상태·헤더를 바꾸지 않는
     * 나머지 메서드는 DTO를 그대로 반환한다(동일하게 200).
     */
    @DeleteMapping("/sessions/{sessionId}")
    public ResponseEntity<Void> deleteSession(
            @PathVariable("sessionId") String sessionId,
            @RequestParam(name = "userId") String userId) {
        chatService.deleteSession(sessionId, userId);
        return ResponseEntity.noContent().build();
    }
}
