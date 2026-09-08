package com.yiji.Chatbot.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 프론트엔드에서 들어오는 채팅 질문 요청 DTO
 *
 * 요청자 식별자는 여기 없다. `X-User-Id` 헤더로 받는다(@CurrentUser).
 * 신원은 "무엇을 물었는가"와 다른 층위의 정보이고, 모든 엔드포인트가 공통으로 필요로 한다.
 *
 * @param sessionId 대화방 ID. 첫 질문이면 null 또는 빈 값으로 오고 서버가 새로 발급한다.
 * @param query     질문 본문
 */
public record ChatRequestDto(

        String sessionId,

        @NotBlank(message = "질문 내용을 입력해주세요.")
        @Size(max = 1000, message = "질문은 최대 1000자까지 입력 가능합니다.")
        String query
) {
}
