package com.yiji.Chatbot.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 프론트엔드에서 들어오는 채팅 질문 요청 DTO
 *
 * @param sessionId 대화방 ID. 첫 질문이면 null 또는 빈 값으로 오고 서버가 새로 발급한다.
 * @param userId    사용자 식별자(브라우저 익명 ID). 사용자별 대화방 격리에 쓴다.
 * @param query     질문 본문
 */
public record ChatRequestDto(

        String sessionId,

        @NotBlank(message = "사용자 식별자가 필요합니다.")
        String userId,

        @NotBlank(message = "질문 내용을 입력해주세요.")
        @Size(max = 1000, message = "질문은 최대 1000자까지 입력 가능합니다.")
        String query
) {
}
