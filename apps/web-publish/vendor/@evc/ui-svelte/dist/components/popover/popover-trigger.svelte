<script lang="ts">
  import { Popover as PopoverPrimitive } from 'bits-ui';

  type Props = PopoverPrimitive.TriggerProps & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<PopoverPrimitive.Trigger {...restProps}>
  {@render children?.()}
</PopoverPrimitive.Trigger>
