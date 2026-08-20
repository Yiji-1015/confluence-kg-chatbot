package com.yiji.Chatbot.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 대화 내역 1건을 나타내는 DTO
 * - 특정 대화방을 열었을 때 과거 전체 질문/답변 목록을 보여주거나, Redis 세션 캐싱에 사용됩니다.
 */
@Getter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ChatMessageDto {

    private Long id;                         // 메시지 ID
    private String role;                     // "USER" 또는 "ASSISTANT"
    private String content;                  // 메시지 본문
    private List<SourceDocumentDto> sources; // RAG 출처 목록 (ASSISTANT 답변일 때만 존재)
    private LocalDateTime createdAt;         // 작성 시각
}
