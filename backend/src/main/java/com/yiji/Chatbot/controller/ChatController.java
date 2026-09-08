package com.yiji.Chatbot.controller;

import com.yiji.Chatbot.dto.ChatMessageDto;
import com.yiji.Chatbot.dto.ChatRequestDto;
import com.yiji.Chatbot.dto.ChatResponseDto;
import com.yiji.Chatbot.dto.ChatSessionDto;
import com.yiji.Chatbot.service.ChatService;
import com.yiji.Chatbot.web.CurrentUser;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.data.web.PageableDefault;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * Confluence RAG Chatbot 메인 REST API 컨트롤러
 *
 * 요청자 식별은 `X-User-Id` 헤더로 받는다(@CurrentUser). 쿼리 파라미터로 받던 때는
 * 신원이 URL에 실려 액세스 로그에 남았고, 모든 시그니처에 `userId`가 반복됐다.
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
    public ChatResponseDto chat(@CurrentUser String userId,
                                @Valid @RequestBody ChatRequestDto requestDto) {
        return chatService.processChat(requestDto, userId);
    }

    /**
     * 대화방 목록 조회 (좌측 사이드바용, 최근 대화 순)
     * GET /api/sessions?page=0&size=50
     *
     * 전부 반환하지 않는다. 대화방이 쌓일수록 응답이 무한정 커지는데 사이드바는
     * 최근 것만 보여준다. 총 개수는 응답의 totalElements로 함께 나간다.
     */
    @GetMapping("/sessions")
    public Page<ChatSessionDto> getSessions(
            @CurrentUser String userId,
            @PageableDefault(size = 50, sort = "updatedAt", direction = Sort.Direction.DESC)
            Pageable pageable) {
        return chatService.getSessions(userId, pageable);
    }

    /**
     * 특정 대화방의 과거 전체 메시지 내역 조회
     * GET /api/sessions/{sessionId}/messages
     */
    @GetMapping("/sessions/{sessionId}/messages")
    public List<ChatMessageDto> getMessages(@CurrentUser String userId,
                                            @PathVariable("sessionId") String sessionId) {
        return chatService.getMessages(sessionId, userId);
    }

    /**
     * 대화방 삭제
     * DELETE /api/sessions/{sessionId}
     *
     * 본문 없이 204를 주므로 ResponseEntity를 쓴다. 상태·헤더를 바꾸지 않는
     * 나머지 메서드는 DTO를 그대로 반환한다(동일하게 200).
     */
    @DeleteMapping("/sessions/{sessionId}")
    public ResponseEntity<Void> deleteSession(@CurrentUser String userId,
                                              @PathVariable("sessionId") String sessionId) {
        chatService.deleteSession(sessionId, userId);
        return ResponseEntity.noContent().build();
    }
}
