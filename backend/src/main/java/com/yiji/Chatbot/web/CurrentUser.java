package com.yiji.Chatbot.web;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 요청자의 식별자를 컨트롤러 파라미터로 주입받는다.
 *
 * 값은 `X-User-Id` 헤더에서 온다. 예전에는 모든 엔드포인트가 `userId`를 쿼리 파라미터로
 * 받았는데 두 가지가 걸렸다.
 *
 * - 신원이 URL에 실려 액세스 로그·프록시 로그·브라우저 히스토리에 그대로 남는다.
 *   특히 DELETE 요청까지 `?userId=...`가 붙었다.
 * - 모든 컨트롤러와 서비스 시그니처에 `userId`가 반복됐고, 누구도 빈 값을 검증하지 않았다.
 */
@Target(ElementType.PARAMETER)
@Retention(RetentionPolicy.RUNTIME)
public @interface CurrentUser {
}
