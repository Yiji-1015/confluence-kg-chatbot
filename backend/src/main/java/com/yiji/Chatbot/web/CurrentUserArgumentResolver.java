package com.yiji.Chatbot.web;

import org.springframework.core.MethodParameter;
import org.springframework.stereotype.Component;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;

/**
 * `@CurrentUser String userId` 파라미터를 `X-User-Id` 헤더에서 채운다.
 *
 * 헤더가 없거나 비어 있으면 Spring 표준 예외를 던진다. GlobalExceptionHandler가 상속한
 * ResponseEntityExceptionHandler가 이미 400 ProblemDetail로 번역하므로 따로 처리기를
 * 만들지 않는다.
 */
@Component
public class CurrentUserArgumentResolver implements HandlerMethodArgumentResolver {

    public static final String HEADER = "X-User-Id";

    @Override
    public boolean supportsParameter(MethodParameter parameter) {
        return parameter.hasParameterAnnotation(CurrentUser.class)
                && String.class.equals(parameter.getParameterType());
    }

    @Override
    public Object resolveArgument(MethodParameter parameter,
                                  ModelAndViewContainer mavContainer,
                                  NativeWebRequest webRequest,
                                  WebDataBinderFactory binderFactory) throws Exception {
        String userId = webRequest.getHeader(HEADER);
        if (userId == null || userId.isBlank()) {
            throw new MissingRequestHeaderException(HEADER, parameter);
        }
        return userId.trim();
    }
}
