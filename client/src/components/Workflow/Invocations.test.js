import Invocations from "../Workflow/Invocations";
import { mount } from "@vue/test-utils";
import { getLocalVue } from "jest/helpers";
import invocationData from "./test/json/invocation.json";
import moment from "moment";

jest.mock("components/History/caching");

const localVue = getLocalVue();

describe("Invocations.vue without invocation", () => {
    let wrapper;
    let propsData;

    beforeEach(async () => {
        propsData = {
            ownerGrid: false,
            loading: true,
        };
        wrapper = mount(Invocations, {
            propsData,
            localVue,
        });
    });

    it("loading should be true", async () => {
        expect(wrapper.vm.loading).toBeTruthy();
    });

    it("title should be shown", async () => {
        expect(wrapper.find("#invocations-title").text()).toBe("Workflow Invocations");
    });

    it("no invocations message should not be shown", async () => {
        expect(wrapper.find("#no-invocations").exists()).toBe(false);
    });

    it("no invocations message should be shown when not loading", async () => {
        propsData.loading = false;
        await wrapper.setProps({ loading: false });
        expect(wrapper.find("#no-invocations").exists()).toBe(true);
    });
});

describe("Invocations.vue with invocation", () => {
    let wrapper;
    let propsData;

    beforeEach(async () => {
        propsData = {
            ownerGrid: false,
            loading: false,
            invocationItems: [invocationData],
        };
        wrapper = mount(Invocations, {
            propsData,
            computed: {
                getWorkflowNameByInstanceId: (state) => (id) => "workflow name",
                getWorkflowByInstanceId: (state) => (id) => {
                    return { id: "workflowId" };
                },
                getHistoryNameById: () => () => "history name",
            },
            stubs: {
                "workflow-invocation-state": {
                    template: "<span/>",
                },
            },
            localVue,
        });
    });

    it("renders one row", async () => {
        const rows = wrapper.findAll("tbody > tr").wrappers;
        expect(rows.length).toBe(1);
        const row = rows[0];
        const columns = row.findAll("td");
        expect(columns.at(0).text()).toBe("workflow name");
        expect(columns.at(1).text()).toBe("history name");
        expect(columns.at(2).text()).toBe(moment.utc(invocationData.create_time).fromNow());
        expect(columns.at(3).text()).toBe(moment.utc(invocationData.update_time).fromNow());
        expect(columns.at(4).text()).toBe("scheduled");
        expect(columns.at(5).text()).toBe("");
    });

    it("toggles detail rendering", async () => {
        let rows = wrapper.findAll("tbody > tr").wrappers;
        expect(rows.length).toBe(1);
        await wrapper.find(".toggle-invocation-details").trigger("click");
        rows = wrapper.findAll("tbody > tr").wrappers;
        expect(rows.length).toBe(3);
        await wrapper.find(".toggle-invocation-details").trigger("click");
        rows = wrapper.findAll("tbody > tr").wrappers;
        expect(rows.length).toBe(1);
    });

    it("calls switchHistory", async () => {
        const mockMethod = jest.fn();
        wrapper.vm.switchHistory = mockMethod;
        await wrapper.find("#switch-to-history").trigger("click");
        expect(mockMethod).toHaveBeenCalled();
    });

    it("calls executeWorkflow", async () => {
        const mockMethod = jest.fn();
        wrapper.vm.executeWorkflow = mockMethod;
        await wrapper.find("#run-workflow").trigger("click");
        expect(mockMethod).toHaveBeenCalledWith("workflowId");
    });
});
