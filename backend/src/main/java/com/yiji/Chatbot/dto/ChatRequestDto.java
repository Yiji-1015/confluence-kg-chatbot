package com.yiji.Chatbot.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 프론트엔드에서 들어오는 채팅 질문 요청 DTO
 */
@Getter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ChatRequestDto {

    /**
     * 대화방 ID (UUID)
     * - 첫 질문(새 대화방)일 경우 null 또는 빈 값으로 전달되며, 백엔드에서 새로 발급합니다.
     * - 이어지는 질문일 경우 기존 발급받은 sessionId를 그대로 전달합니다.
     */
    private String sessionId;

    /**
     * 사용자 식별자 (브라우저 익명 ID 또는 사원 ID)
     * - 사용자별 대화방 목록을 격리하는 데 사용됩니다.
     */
    @NotBlank(message = "사용자 식별자가 필요합니다.")
    private String userId;

    @NotBlank(message = "질문 내용을 입력해주세요.")
    @Size(max = 1000, message = "질문은 최대 1000자까지 입력 가능합니다.")
    private String query;
}
