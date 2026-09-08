package com.yiji.Chatbot.controller;

import com.yiji.Chatbot.exception.AiEngineException;
import com.yiji.Chatbot.exception.SessionNotFoundException;
import com.yiji.Chatbot.service.ChatService;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.not;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * GlobalExceptionHandler가 실패 이유를 사용자에게 실어 보내는지 확인한다.
 *
 * 이 처리기가 없으면 검증 실패는 Boot 기본 에러 JSON으로 나가는데,
 * 거기엔 message가 아예 빠져 있다(ErrorProperties.includeMessage 기본값이 NEVER).
 * 즉 DTO에 적어둔 문구가 사용자에게 도달할 경로가 없었다.
 */
@WebMvcTest(ChatController.class)
class ChatControllerErrorHandlingTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private ChatService chatService;

    @Test
    @DisplayName("1000자를 넘는 질문은 400과 함께 DTO에 적어둔 메시지를 돌려준다")
    void tooLongQueryReturnsMessage() throws Exception {
        String body = """
                {"userId": "u-1", "query": "%s"}
                """.formatted("가".repeat(1001));

        mockMvc.perform(post("/api/chat").contentType(APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_PROBLEM_JSON))
                .andExpect(jsonPath("$.detail").value("질문은 최대 1000자까지 입력 가능합니다."))
                .andExpect(jsonPath("$.errors.query").value("질문은 최대 1000자까지 입력 가능합니다."));
    }

    @Test
    @DisplayName("userId가 비어 있으면 400과 함께 해당 필드 메시지를 돌려준다")
    void blankUserIdReturnsMessage() throws Exception {
        String body = """
                {"userId": "", "query": "안녕하세요"}
                """;

        mockMvc.perform(post("/api/chat").contentType(APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors.userId").value("사용자 식별자가 필요합니다."));
    }

    @Test
    @DisplayName("서비스가 던진 SessionNotFoundException은 404 ProblemDetail로 번역된다")
    void sessionNotFoundBecomes404() throws Exception {
        given(chatService.getMessages(anyString(), anyString()))
                .willThrow(new SessionNotFoundException("s-404"));

        mockMvc.perform(get("/api/sessions/s-404/messages").param("userId", "u-1"))
                .andExpect(status().isNotFound())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_PROBLEM_JSON))
                .andExpect(jsonPath("$.title").value("대화방을 찾을 수 없음"))
                .andExpect(jsonPath("$.detail").value("대화방을 찾을 수 없습니다. (sessionId: s-404)"));
    }

    @Test
    @DisplayName("AI 엔진 실패는 502가 되고 내부 주소·예외 메시지는 응답에 실리지 않는다")
    void aiEngineFailureBecomes502() throws Exception {
        given(chatService.processChat(any()))
                .willThrow(new AiEngineException(
                        "AI 서버 호출 실패 (sessionId: s-1)",
                        new RuntimeException("Connection refused: http://ai-server:8000/internal/chat")));

        String body = """
                {"userId": "u-1", "query": "안녕하세요"}
                """;

        mockMvc.perform(post("/api/chat").contentType(APPLICATION_JSON).content(body))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.detail")
                        .value("AI 검색 엔진에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도해주세요."))
                // 3번 항목의 요점. 원인 메시지에는 내부 AI 서버 주소가 들어 있다.
                .andExpect(content().string(not(containsString("ai-server"))))
                .andExpect(content().string(not(containsString("Connection refused"))));
    }
}
