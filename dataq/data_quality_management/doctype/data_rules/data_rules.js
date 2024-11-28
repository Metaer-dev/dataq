// Copyright (c) 2024, Tiger and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Data Rules", {
// 	refresh(frm) {

// 	},
// });



frappe.ui.form.on('Data Rules', {
    which_gx(frm, ab, cd, ef) {
        let which = frm.doc.which_gx;
        frm.call({
            method: "dataq.data_quality_management.api.get_child_table_data",
            args: {
                parent_doctype: "GX Function",
                parent_name: which,
                child_table_fieldname: "args"
            }
        }).then(res=>{
            if (res.message) {
                cur_frm.set_value('args', null) 
                message = res.message
                message.forEach(one => {
                    let args = frm.add_child('args')
                    args.args_name = one.args_name
                    args.args_type = one.python_type
                })
                frm.refresh_field('args');
            }
        })
    }
})

