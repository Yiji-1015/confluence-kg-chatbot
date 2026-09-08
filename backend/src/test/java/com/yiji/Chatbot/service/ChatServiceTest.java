package com.yiji.Chatbot.service;

import com.yiji.Chatbot.dto.ChatRequestDto;
import com.yiji.Chatbot.dto.ChatResponseDto;
import com.yiji.Chatbot.dto.InternalChatDto;
import com.yiji.Chatbot.exception.AiEngineException;
import com.yiji.Chatbot.mapper.ChatMapper;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

/**
 * 트랜잭션 경계를 나눈 뒤의 처리 순서를 고정한다.
 *
 * 순서가 흐트러지면 조용히 망가지는 것들이라 테스트로 붙잡아 둔다.
 * - AI 호출보다 먼저 DB에 쓰면, 실패한 턴의 흔적이 남는다.
 * - DB보다 Redis를 먼저 쓰면, 롤백돼도 Redis에 남아 다음 턴 컨텍스트가 오염된다.
 * - 소유권 확인 전에 Redis를 읽으면, 남의 대화 맥락이 LLM에 넘어간다.
 */
@ExtendWith(MockitoExtension.class)
class ChatServiceTest {

    @Mock
    private ChatPersistenceService chatPersistenceService;
    @Mock
    private RedisSessionService redisSessionService;
    @Mock
    private AiEngineClient aiEngineClient;
    @Mock
    private ChatMapper chatMapper;

    private ChatService chatService;

    @BeforeEach
    void setUp() {
        chatService = new ChatService(
                new SimpleMeterRegistry(), chatPersistenceService, redisSessionService, aiEngineClient, chatMapper);
    }

    private InternalChatDto.Response aiAnswers(String answer) {
        return InternalChatDto.Response.builder().answer(answer).sources(List.of()).build();
    }

    @Test
    @DisplayName("sessionId 없이 오면 서버가 새 ID를 발급하고 히스토리는 읽지 않는다")
    void newConversationIssuesServerSideId() {
        given(chatPersistenceService.canContinue(null, "u-1")).willReturn(false);
        given(aiEngineClient.requestChat(anyString(), anyString(), any())).willReturn(aiAnswers("답변"));

        ChatResponseDto response = chatService.processChat(new ChatRequestDto(null, "u-1", "질문"));

        assertThat(response.sessionId()).isNotBlank();
        verify(redisSessionService, never()).getRecentHistory(anyString());
        verify(chatPersistenceService, never()).loadRecentHistory(anyString());
    }

    @Test
    @DisplayName("알 수 없는 sessionId가 오면 그 ID를 쓰지 않고 새로 발급한다")
    void unknownSessionIdIsNotReused() {
        given(chatPersistenceService.canContinue("client-made-up-id", "u-1")).willReturn(false);
        given(aiEngineClient.requestChat(anyString(), anyString(), any())).willReturn(aiAnswers("답변"));

        ChatResponseDto response =
                chatService.processChat(new ChatRequestDto("client-made-up-id", "u-1", "질문"));

        assertThat(response.sessionId()).isNotEqualTo("client-made-up-id");
    }

    @Test
    @DisplayName("저장은 DB가 먼저고 Redis가 나중이다")
    void persistsToDatabaseBeforeRedis() {
        given(chatPersistenceService.canContinue("s-1", "u-1")).willReturn(true);
        given(redisSessionService.getRecentHistory("s-1")).willReturn(List.of());
        given(chatPersistenceService.loadRecentHistory("s-1")).willReturn(List.of());
        given(aiEngineClient.requestChat(anyString(), anyString(), any())).willReturn(aiAnswers("답변"));

        chatService.processChat(new ChatRequestDto("s-1", "u-1", "질문"));

        InOrder order = inOrder(chatPersistenceService, redisSessionService);
        order.verify(chatPersistenceService).saveTurn("s-1", "u-1", "질문", "답변", null);
        order.verify(redisSessionService).saveTurn("s-1", "질문", "답변");
    }

    @Test
    @DisplayName("AI 호출이 실패하면 DB에도 Redis에도 아무것도 남지 않는다")
    void failedAiCallLeavesNoTrace() {
        given(chatPersistenceService.canContinue(null, "u-1")).willReturn(false);
        given(aiEngineClient.requestChat(anyString(), anyString(), any()))
                .willThrow(new AiEngineException("AI 서버 호출 실패"));

        assertThatThrownBy(() -> chatService.processChat(new ChatRequestDto(null, "u-1", "질문")))
                .isInstanceOf(AiEngineException.class);

        verify(chatPersistenceService, never()).saveTurn(anyString(), anyString(), anyString(), anyString(), any());
        verify(redisSessionService, never()).saveTurn(anyString(), anyString(), anyString());
    }

    @Test
    @DisplayName("대화방 삭제는 DB 삭제가 끝난 뒤 Redis 캐시를 지운다")
    void deletesFromDatabaseBeforeRedis() {
        chatService.deleteSession("s-1", "u-1");

        InOrder order = inOrder(chatPersistenceService, redisSessionService);
        order.verify(chatPersistenceService).deleteSession("s-1", "u-1");
        order.verify(redisSessionService).clearSession("s-1");
    }
}
