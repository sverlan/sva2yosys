// Glue logic for counter_props (included relative to counter_props.sv).
logic counting;
assign counting = en && cnt != 4'd15;
