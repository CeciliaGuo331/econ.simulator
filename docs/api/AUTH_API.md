# 用户注册与登录 API

本文档描述基于邮箱 + 密码的基础身份认证能力。当前实现不涉及验证码或邮件发送，可在后续扩展。

## 1. 注册用户

`POST /auth/register`

请求体：

```json
{
  "email": "player@example.com",
  "password": "StrongPass123"
}
```

- 密码长度要求 8~128 个字符。
- 邮箱将被归一化为小写并作为唯一标识。
- 如邮箱已存在，返回 `409 Conflict`。

响应：

```json
{
  "user_id": "player@example.com",
  "message": "Registration successful."
}
```

## 2. 用户登录

`POST /auth/login`

请求体：

```json
{
  "email": "player@example.com",
  "password": "StrongPass123"
}
```

响应：

```json
{
  "access_token": "0f8f8f4a0c2843e2a13d4f2f0d5a7df7",
  "token_type": "bearer"
}
```

- 登录失败（邮箱不存在或密码错误）将返回 `401 Unauthorized`。
- `access_token` 为当前会话的随机字符串，未来可用于调用需要身份验证的接口。

## 3. 常见错误

| 状态码 | 场景                    | 提示信息                     |
| ------ | ----------------------- | ---------------------------- |
| 400    | 请求体字段缺失或非法    | Pydantic 自动生成的错误描述   |
| 409    | 邮箱重复注册            | `Email already registered`   |
| 401    | 邮箱或密码不匹配        | `Invalid email or password`  |

---
未来可在此基础上扩展邮箱验证、密码找回、多因素认证等能力。