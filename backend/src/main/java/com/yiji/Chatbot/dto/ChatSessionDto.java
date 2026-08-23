package com.yiji.Chatbot.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 좌측 사이드바에 표시할 대화방 목록 1개 항목 DTO
 */
@Getter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ChatSessionDto {

    private String id;               // 대화방 ID (UUID)
    private String userId;           // 사용자 ID
    private String title;            // 대화방 제목 (첫 질문 요약)
    private LocalDateTime createdAt; // 대화방 생성 시각
    private LocalDateTime updatedAt; // 마지막 대화 시각
}
