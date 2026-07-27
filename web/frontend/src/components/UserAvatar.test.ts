import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import UserAvatar from "./UserAvatar.vue";

describe("UserAvatar", () => {
  it("renders stable initials in one text node", () => {
    const english = mount(UserAvatar, { props: { name: "Ada Lovelace" } });
    const chinese = mount(UserAvatar, { props: { name: "张三丰" } });
    expect(english.text()).toBe("AL");
    expect(english.element.childNodes).toHaveLength(1);
    expect(chinese.text()).toBe("张三");
  });
});
